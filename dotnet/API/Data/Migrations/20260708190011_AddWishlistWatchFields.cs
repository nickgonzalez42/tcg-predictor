using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddWishlistWatchFields : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<double>(
                name: "AlertTargetPrice",
                table: "TrackedCards",
                type: "REAL",
                nullable: true);

            migrationBuilder.AddColumn<double>(
                name: "WatchedAtPrice",
                table: "TrackedCards",
                type: "REAL",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "AlertTargetPrice",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "WatchedAtPrice",
                table: "TrackedCards");
        }
    }
}
